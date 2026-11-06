# HTTP API reference

Every route here is real and comes from `api.py` and `view.py`. `tests/test_docs_reference.py` fails if a
route is added, removed or renamed without this page changing. Money is in dollars in requests and
responses.

There are two surfaces with different authentication, on purpose.

## Operator surface: bearer token

Mounted under `/api/plugins/inventory`. The console view and anything you script use these. They need the
instance's operator bearer token, which the view gets from the console's handshake:

```bash
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:7870/api/plugins/inventory/summary
```

Through a fleet hub, prefix the path with `/agents/<agent-id>`.

### Lots, items and prices

| Method | Path | What it does |
|---|---|---|
| `GET` | `/summary` | The P&L per lot and in total: cost, remaining at low/target/high, realized gross and net, projected net |
| `GET` | `/lots` | Every lot |
| `PUT` | `/lots/{lot_id}` | Create or update a lot. Only the fields sent change |
| `DELETE` | `/lots/{lot_id}` | Delete a lot. Refused while it has items unless `cascade=true`, which snapshots each removed item into the audit trail |
| `GET` | `/lots/{lot_id}/split-check` | Sealed vs split for a lot whose pieces are all still planned |
| `GET` | `/systems` | The game systems in use, for the system picker |
| `GET` | `/conditions` | The condition suggestions plus every condition in use |
| `GET` | `/items` | Items, filtered by lot, status, category, system, bin or a text search |
| `POST` | `/items` | Create an item |
| `GET` | `/items/{item_id}` | One item with its listings, sales and price observations |
| `PUT` | `/items/{item_id}` | Update an item's fields. Setting sold here is refused: record a sale instead |
| `DELETE` | `/items/{item_id}` | Delete an item and its listings, sales and observations (snapshotted into the audit trail) |
| `POST` | `/items/{item_id}/price` | Set the price band. A `basis` is required, and the comps evidence is stored with it |
| `GET` | `/stale` | Listings live longer than `listed_days` and prices older than `price_days` (default: the Stale price setting) |
| `GET` | `/aging` | Unsold items past an aging tier, oldest first, each with the basis its days count from |
| `POST` | `/items/{item_id}/snooze` | Hide an item from Aging stock for 14 days (audited) |
| `PUT` | `/items/{item_id}/shipping` | Set packed weight, box size and packaging preset |
| `GET` | `/view-config` | The settings the view needs: the stale-price days and the aging tiers |

### Selling

| Method | Path | What it does |
|---|---|---|
| `POST` | `/items/{item_id}/sold` | Record a sale (the same rules as `inventory_mark_sold`) |
| `GET` | `/sales` | Sales, optionally for one lot |
| `POST` | `/items/{item_id}/listings` | Record a live listing on a channel |
| `POST` | `/listings/{listing_id}/end` | End a listing, including one flagged `needs_end` ("I ended it") |
| `GET` | `/listings/to-end` | The double-sell guard: listings still up somewhere for items that sold elsewhere |
| `GET` | `/to-pack` | Sales not yet packed, grouped by the bin to pull from |
| `POST` | `/sales/{sale_id}/packed` | Mark a sale packed. An item with no stock left leaves its bin |
| `GET` | `/sale-pastes` | Pasted sales waiting to be checked |
| `POST` | `/sale-pastes` | Paste an order or a note. Starts one agent turn that reads it |
| `GET` | `/sale-pastes/{paste_id}` | One paste with its proposal |
| `POST` | `/sale-pastes/{paste_id}/retry` | Read a failed paste again |
| `POST` | `/sale-pastes/{paste_id}/record` | Record the checked lines, one audited sale each |
| `DELETE` | `/sale-pastes/{paste_id}` | Discard a paste (it's kept, marked discarded) |

### Listing drafts

| Method | Path | What it does |
|---|---|---|
| `GET` | `/drafts` | Every item's draft state |
| `GET` | `/items/{item_id}/draft` | One item's draft |
| `POST` | `/items/{item_id}/draft` | Ask for a draft. Starts one agent turn |
| `POST` | `/drafts/bulk` | Draft several items, one turn after another, at most 10 per request |
| `POST` | `/items/{item_id}/draft/copied` | Note that you copied the draft |
| `POST` | `/items/{item_id}/draft/sku-updated` | Note that you updated the Custom label on eBay after a bin move |
| `POST` | `/items/{item_id}/draft/live` | "I listed it": the eBay item address records a listing and marks the item listed |
| `GET` | `/items/{item_id}/photos.zip` | The item's photos in listing order, cover first |

### Scans

| Method | Path | What it does |
|---|---|---|
| `GET` | `/scans` | Open scans (or all with `open=false`) |
| `POST` | `/scans` | Start a scan: a `box` (with what you paid, which starts its lot) or a `unit` |
| `GET` | `/scans/{scan_id}` | One scan with its proposal |
| `POST` | `/scans/{scan_id}/photos` | Upload a photo (the raw image as the body, 20 MB at most). Starts identification |
| `GET` | `/scans/{scan_id}/photos/{photo_id}` | One stored photo |
| `POST` | `/scans/{scan_id}/retry` | Identify again |
| `POST` | `/scans/{scan_id}/confirm` | Confirm the name and Keep or Sell for each piece; writes the items |
| `POST` | `/scans/{scan_id}/price` | Price the sell pile. Starts one agent turn |
| `POST` | `/scans/{scan_id}/sell-sealed` | Replace the planned pieces with one sealed item at the sealed price |
| `POST` | `/scans/{scan_id}/finish` | Finish the scan |
| `DELETE` | `/scans/{scan_id}` | Discard a scan, its photos and an empty lot it started |

### Bins and labels

| Method | Path | What it does |
|---|---|---|
| `GET` | `/bins` | Every bin with its count, unsold value and when it was last checked |
| `POST` | `/bins` | Create bins |
| `PUT` | `/bins/{bin_id}` | Change a bin's label or note |
| `DELETE` | `/bins/{bin_id}` | Delete an empty bin |
| `POST` | `/bins/move` | Move items into a bin. `put_away: true` also makes a planned piece available |
| `POST` | `/bins/{bin_id}/check` | Save a bin check: strays to move in, unscanned items to flag missing |
| `POST` | `/items/{item_id}/found` | Clear an item's missing flag |
| `GET` | `/labels/base` | Where label QR codes point, and the label stocks |
| `POST` | `/labels/sheet` | Build a label sheet for bins or items, record what was printed, and return a short-lived link to it |

### Packaging

| Method | Path | What it does |
|---|---|---|
| `GET` | `/packaging` | Your packaging presets |
| `POST` | `/packaging` | Add a preset: name, length/width/height in inches, empty weight |
| `PUT` | `/packaging/{packaging_id}` | Change a preset |
| `DELETE` | `/packaging/{packaging_id}` | Delete a preset |

### Data

| Method | Path | What it does |
|---|---|---|
| `GET` | `/audit` | The audit trail, newest first, optionally for one entity |
| `POST` | `/import` | Import CSV text (items or lots). Re-import never clobbers |
| `GET` | `/export` | Export items, lots or sales as CSV |

## Public surface: no token

Mounted under `/plugins/inventory`. The console frames the view with a plain navigation, and a phone opens
labels from a printed QR, so neither can carry a bearer token. None of these routes reads inventory data.
The host exempts them because the manifest lists them under `public_paths` (the view is exempt because it's
a declared view).

| Method | Path | What it does |
|---|---|---|
| `GET` | `/view` | The console view's page. Its data comes from the operator surface |
| `GET` | `/b/{bin_id}` | A bin label's landing page. A browser signed in to this console goes straight to the bin; anyone else sees how to open it in the app. The same response for a bin that exists and one that doesn't |
| `GET` | `/i/{item_id}` | An item label's landing page, the same way |
| `GET` | `/sheet/{token}` | A label sheet by its 15-minute token, for printing in the browser. An unknown or expired token answers a short page with no data |

The landing pages and sheets are sent with `Cache-Control: no-store`, `Referrer-Policy: no-referrer` and
`X-Robots-Tag: noindex`.
