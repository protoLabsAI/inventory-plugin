---
name: inventory-ops
description: How to keep the resale inventory honest — record what was bought, what it split into, what each piece should sell for and WHY, what sold and for how much net. Use whenever the operator mentions stock, a lot, a listing, a sale, targets, or asks how a lot is doing.
---

# Inventory operations

The inventory database is the source of truth. Not the chat, not a CSV in the workspace,
not a note. If a number is not in the inventory, it has not been decided.

## Shape

- **Lot** — a purchase. Cost, date, source. P&L is computed against the lot cost.
- **Item** — one sellable thing from a lot (a team, a dice set, a rulebook). Has a status:
  `planned` (exists once a sealed box is split) → `available` → `listed` → `pending` →
  `sold`, or `kept` / `withdrawn`.
- **Targets** — low / target / high in dollars, ALWAYS with a `price_basis` string and a
  date. Set them with `inventory_set_price`, never by editing fields directly.
- **Price observations** — the comps behind a target (source, n, p25/median/p75, query).
  `inventory_set_price` records one when you pass the comps. This is what makes a later
  re-price explainable.
- **Listings** — where an item is up (channel, url, price). `inventory_listing add|end`.
- **Sales** — what it actually sold for. `inventory_mark_sold` computes
  `net = price + shipping charged − fees − shipping cost` and closes live listings.

## Rules

1. **A target without a basis is not a target.** "eBay sold comps (22 sold, incl.
   shipping)" is a basis. "Retail anchor, no sold comps" is a basis. A bare number is not.
2. **Sold ≠ active ≠ retail.** When the basis is asking prices or retail, say so every
   time you quote the number. Only sold comps say what buyers paid.
3. **Never set status=sold by hand.** Use `inventory_mark_sold` so the sale and the net
   are recorded. The tools refuse the shortcut.
4. **Money in dollars in tool calls; the store keeps cents.** Do not round yourself.
5. **Ask before deleting.** `withdrawn` and `kept` exist so the record survives.
6. **Report with `inventory_summary`.** It is the one place lot cost, remaining value and
   realized net are reconciled. Quote its `projected_net_at_target` with the caveat that
   remaining value is at target, not sold.

## Re-pricing an item from eBay sold comps

1. `ebay_price_check` (sold) with a buyer-style query. Read `results_found`,
   `headline_count` and `notes` first: zero exact matches or a large
   `related_rows_excluded` next to a small `results_found` means the query needs
   broadening, not that the item is worth the padded median.
2. With ≥ 5 exact sold comps: `inventory_set_price(item_id, basis="eBay sold comps (N sold,
   incl. shipping)", low=p25, target=median, high=p75, source="ebay_sold", n=N, p25=…,
   median=…, p75=…, query="…")`.
3. With fewer: leave the targets, and record the thin observation anyway
   (`inventory_set_price` with the existing targets and a basis that says "sold comps thin
   (N)") so the attempt is on file.

## Weekly review

`inventory_stale` lists listings older than 14 days and items whose price evidence is
older than 30 days. Re-price the stale prices, ask the operator about the stale listings
(drop the price, relist, bundle, or withdraw), and finish with `inventory_summary`.

## Importing a spreadsheet

`inventory_import_csv(path=…)` maps common headers itself and reports `mapped_columns`,
`ignored_columns` and per-line `warnings`. Read the warnings back to the operator; a
row that failed is a row that is not in the inventory.
