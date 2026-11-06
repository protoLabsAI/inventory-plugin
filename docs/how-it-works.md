# How it works

What the plugin stores, how it decides things, and what it asks the agent to do. The reasoning behind each
choice is in [design-decisions.md](design-decisions.md).

## One database is the source of truth

Everything lives in one SQLite file the plugin owns, `inventory.db`, in the host's instance-scoped plugin store
(or at `db_path`). Each agent and the dev sandbox get their own. Scan photos are kept beside it, stripped of
metadata. Spreadsheets are an import and an export, never the record.

Every write is one transaction under a process lock, so a parallel tool call can't half-apply.

## The data model

| Thing | What it is |
|---|---|
| **Lot** | A purchase: what it cost, when, from where. P&L is worked out against the lot's cost |
| **Item** | One sellable thing from a lot, with its game system, condition, quantity, status, bin, price band and the basis for that band |
| **Price observation** | The comps behind a price: source, count, p25 / median / p75 and the search used. Written with the price, in the same transaction |
| **Listing** | Where an item is up for sale: channel, address, price, state |
| **Sale** | What it actually sold for, with shipping charged, fees and postage. Net = price + shipping charged − fees − shipping cost |
| **Bin** | Where items physically are (B01, B02…), with a label, a note and when it was last checked |
| **Scan** | A photographed box or piece, the agent's proposal, and your correction |
| **Draft** | An item's eBay listing draft and its state |
| **Sale paste** | A pasted order, the agent's reading of it, and what you recorded |
| **Packaging** | Your packaging presets: name, box size, empty weight |
| **Audit** | Every change, who made it (`agent` or `console`) and the fields that changed |

### Item statuses

| Status | Meaning |
|---|---|
| `planned` | A piece that will exist once a box is split. Its target counts toward the lot, but it isn't for sale yet. Shown as "to split" |
| `available` | For sale |
| `listed` | Live on at least one channel |
| `pending` | Sold but not settled, or on hold for a buyer |
| `sold` | Set only by recording a sale |
| `kept` | You're keeping it |
| `withdrawn` | Out of the sale pile for another reason |

Listings are `active`, `ended`, `sold`, or `needs_end`. `needs_end` means the item sold on a different
channel while this listing was still up. Nothing here can take the listing down for you, so it stays on Next
until you end it and press **I ended it**.

Drafts are `drafting`, `draft`, `copied`, `live` or `failed`.

## Money

Money is stored as integer cents and shown as dollars. Parsing is strict: `12.50`, `$1,234.56` and `12,50`
are amounts; `54%`, `2026-07` and `n/a` are not, and are refused rather than guessed.

## Prices always carry their basis

`inventory_set_price` refuses a price without a `basis`, such as "eBay sold comps (22 sold, incl. shipping)",
and stores the comps with it. A price older than **Stale price** (30 days by default) is badged and counted,
and the weekly review re-checks it.

The weekly review uses at least 5 exact sold comps to set a band (p25, median, p75). Pricing from a scan uses
at least 3. With fewer, the item keeps its price and says why.

## Turns the plugin starts

Some work needs the model, so the plugin starts an agent turn itself with `sdk.run_in_session`. Each runs in
its own session, never your chat, and each is kept narrow so it stays cheap.

| Turn | Session | What it may do | Rough cost in our tests |
|---|---|---|---|
| Identify a scan | `inventory-scan-<id>` | See the photos, at most two web searches. No eBay, no pricing, no subagents | About $0.60 on a frontier model |
| Price a scan | `inventory-scan-<id>-price` | eBay sold comps for each sell piece, and the box sealed | More: one eBay search per piece |
| Write a listing draft | `inventory-draft-<id>` | The item and its photos, at most one web search | About $0.40 |
| Read a pasted sale | `inventory-sale-<id>` | Match items with `inventory_sale_candidates`. No web, no eBay, no subagents | About $0.55 |
| Weekly review | the plugin's scheduled job | Re-price stale evidence, report stale listings, post the P&L. Never changes a listing | Scales with `review_max_items` |

A turn answers through its own tools (`inventory_scan_propose`, `inventory_draft_write`,
`inventory_sale_propose`, and their `_fail` partners). The view polls while one is pending. A turn that never
answers shows as failed with Retry after **Scan: give up after** (pricing gets three times as long).

Photos reach the model through the host's multimodal tool result, so these turns need a vision-capable model.

## Reading a pasted sale safely

- A bundle is split across lines and checked against its total.
- A date like "Delivered Mon Dec 23" must land on that weekday, and no sale can be dated in the future.
- Fees are the stated fee, or an estimate from the ebay plugin marked *estimated*, never invented.
- Buyer names, addresses and tracking numbers are never copied.
- Items sold in the last 180 days are among the candidates, so a repeat paste reads **Already recorded**.

## Labels and their public links

A label's QR encodes `<base>/plugins/inventory/b/<BIN>` or `/i/<ITEM>`. `<base>` is **Bin label address**,
else the agent's public URL, else its console.

These routes are public, because a phone opening a printed QR carries no token. They take only an id, read no
data, and answer the same whether the id exists or not. Each serves a small landing page:

- A browser already signed in to this console (the console's token in this origin's `localStorage`) goes
  straight on to the bin or item.
- Anyone else sees the id, how to open it in the app, and **Sign in here instead**.
- On a host without plugin-view deep links, the page says the console needs an update.

The label never encodes the console's own route, so a change in how the console opens views never makes
printed labels stale.

### Label sheets

`POST /labels/sheet` renders the sheet and returns a link to it: `/plugins/inventory/sheet/<token>`, where
the token is 32 random bytes that live 15 minutes. At most 50 sheets are kept, in memory, so a restart
expires them. A sheet holds only what the printed labels show.

## Reading QR codes

The view reads labels itself, with a bundled decoder (jsQR 1.4.0, Apache-2.0, in `vendor/jsqr`). Nothing is
uploaded. It accepts only this plugin's `/i/` and `/b/` paths, from any host, and never opens a decoded
address.

- **Live camera:** it decodes the centre of the frame about 7 times a second, and the whole frame every third
  time, so an off-centre label still reads. The same label is ignored for 2.5 seconds. The camera stops on
  Done, when the drawer closes, when the view is hidden, and after 2 minutes of nothing.
- **Photo:** it tries the photo at 1,600 px, full size, then 1,000 px.

The live camera works where the view and the console share an origin, as they do through a fleet hub. Where
they don't (the desktop app), or the camera is refused, the view falls back to a photo.

## Bins, put away and packing

- A move is all or nothing and audited with where the item came from. A put-away move also makes a
  `planned` piece `available`.
- A sale waits on **To pack** until you mark it packed. Then an item with no stock left leaves its bin.
- A bin check records `checked_on`. An unscanned item can be flagged missing, which is a date on the item,
  not a status.

## The audit trail

Every write records the actor and the fields that changed. A deleted item or lot is snapshotted into the
trail first, with its listings, sales and observations, so nothing is lost without a record. **More →
History** shows the trail as sentences.
